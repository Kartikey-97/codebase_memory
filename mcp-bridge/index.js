import express from 'express';
import { MongoClient } from 'mongodb';

const app = express();
app.use(express.json({ limit: '50mb' }));

const uri = process.env.MONGODB_URI || 'mongodb://localhost:27017';
let client = new MongoClient(uri, { serverSelectionTimeoutMS: 5000 });

async function getClient() {
  try {
    await client.db('admin').command({ ping: 1 });
  } catch (error) {
    console.warn('MongoDB connection lost. Reconnecting...', error.message);
    client = new MongoClient(uri, { serverSelectionTimeoutMS: 5000 });
    await client.connect();
  }
  return client;
}

app.post('/api/tools/call', async (req, res) => {
  const bridgeToken = process.env.MCP_BRIDGE_TOKEN;
  if (bridgeToken) {
    const authHeader = req.headers.authorization;
    if (authHeader !== `Bearer ${bridgeToken}`) {
      return res.status(401).json({ detail: 'Unauthorized' });
    }
  }

  try {
    const activeClient = await getClient();
    const { tool, arguments: args } = req.body;
    const db = activeClient.db(args.database);
    const col = db.collection(args.collection);
    
    let result = {};
    
    if (tool === 'find') {
      let cursor = col.find(args.filter || {}, { projection: args.projection });
      if (args.sort) {
        let sortObj = {};
        for (const s of args.sort) {
          Object.assign(sortObj, s);
        }
        cursor = cursor.sort(sortObj);
      }
      if (args.limit) cursor = cursor.limit(args.limit);
      const docs = await cursor.toArray();
      result = { documents: docs };
    } 
    else if (tool === 'insert-many') {
      let docsToInsert = args.documents;
      
      if (args.enable_embeddings) {
        // Vertex AI embeddings are handled by the backend directly in production mode.
        // For MCP Bridge development mode, embeddings are skipped unless a local embedding service is wired up.
      }
      
      const insertRes = await col.insertMany(docsToInsert);
      result = { inserted_count: insertRes.insertedCount };
    }
    else if (tool === 'update-many') {
      const updateRes = await col.updateMany(args.filter, args.update);
      result = { matched_count: updateRes.matchedCount, modified_count: updateRes.modifiedCount };
    }
    else if (tool === 'aggregate') {
      const docs = await col.aggregate(args.pipeline).toArray();
      result = { documents: docs };
    }
    else if (tool === 'vector-search') {
      // Vector search requires embeddings. In production, this goes directly through Motor.
      // In dev mode, return empty if no local embedding service is configured.
      result = { documents: [] };
    }
    else {
      return res.status(400).json({ detail: `Unknown tool ${tool}` });
    }
    
    res.json(result);
  } catch (error) {
    console.error(`MCP Error [${req.body.tool}]:`, error);
    res.status(500).json({ detail: error.message });
  }
});

app.get('/health', (req, res) => res.json({ status: 'ok' }));

const port = process.env.PORT || 8081;
app.listen(port, () => console.log(`MCP Bridge running on port ${port}`));
